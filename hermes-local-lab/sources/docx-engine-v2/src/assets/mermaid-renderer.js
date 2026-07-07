const DEFAULT_WIDTH = 1600;
const DEFAULT_HEIGHT = 900;
const FONT_STACK = 'PingFang SC, Microsoft YaHei, Noto Sans CJK SC, WenQuanYi Micro Hei, Arial, sans-serif';

function renderDeterministicMermaidSvg({ figure = {}, sourceText = '' } = {}) {
  const caption = figure.caption || figure.figureId || '图示';
  const source = String(sourceText || '').trim();
  if (!source) {
    throw new Error(`不支持的 Mermaid 图类型: ${figure.figureId || caption} 缺少 Mermaid 源码`);
  }

  const kind = detectMermaidKind(source);
  if (kind === 'c4context') {
    return renderC4ContextSvg({ caption, sourceText: source, diagram: parseC4Context(source) });
  }
  if (kind === 'sequence') {
    return renderSequenceDiagramSvg({ caption, sourceText: source, diagram: parseSequenceDiagram(source) });
  }
  if (kind === 'flowchart') {
    const flowchart = parseMermaidFlowchart(source);
    if (flowchart.nodes.length === 0) {
      throw new Error(`不支持的 Mermaid 图类型: ${figure.figureId || caption} 未解析到可绘制节点`);
    }
    return renderFlowchartSvg({ caption, sourceText: source, flowchart });
  }
  if (kind === 'gantt') {
    return renderGanttSvg({ caption, sourceText: source, diagram: parseGanttDiagram(source) });
  }
  if (kind === 'classdiagram') {
    return renderClassDiagramSvg({ caption, sourceText: source, diagram: parseClassDiagram(source) });
  }
  if (kind === 'mindmap') {
    return renderMindmapSvg({ caption, sourceText: source, diagram: parseMindmap(source) });
  }

  throw new Error(
    `不支持的 Mermaid 图类型: ${kind || figure.figureId || caption}。请改用 flowchart/graph、sequenceDiagram、C4Context、gantt、classDiagram、mindmap，或提供 PNG/SVG 图片资产。`
  );
}

function detectMermaidKind(sourceText) {
  const first = firstMeaningfulLine(sourceText).toLowerCase();
  if (first === 'c4context') return 'c4context';
  if (first === 'sequencediagram') return 'sequence';
  if (/^(flowchart|graph)\b/.test(first)) return 'flowchart';
  return first;
}

function renderC4ContextSvg({ caption, sourceText, diagram }) {
  if (diagram.entities.length === 0) {
    throw new Error(`不支持的 Mermaid 图类型: ${caption} C4Context 未解析到系统节点`);
  }

  const persons = diagram.entities.filter((entity) => entity.type === 'person');
  const externals = diagram.entities.filter((entity) => entity.external);
  const boundaryEntities = diagram.entities.filter((entity) => entity.boundaryId && !entity.external);
  const freeSystems = diagram.entities.filter((entity) => !entity.boundaryId && !entity.external && entity.type !== 'person');
  const boundaries = diagram.boundaries.length > 0
    ? diagram.boundaries.map((boundary) => ({
      ...boundary,
      entities: boundaryEntities.filter((entity) => entity.boundaryId === boundary.id),
    }))
    : [];
  if (freeSystems.length > 0) {
    boundaries.push({ id: '__systems', label: '系统组件', entities: freeSystems });
  }

  const boundaryHeights = boundaries.map((boundary) =>
    Math.max(190, 88 + Math.ceil(Math.max(1, boundary.entities.length) / 2) * 112)
  );
  const centerHeight = boundaryHeights.reduce((sum, value) => sum + value, 0) + Math.max(0, boundaries.length - 1) * 28;
  const sideHeight = Math.max(persons.length, externals.length, 1) * 128;
  const height = Math.max(DEFAULT_HEIGHT, 220 + Math.max(centerHeight, sideHeight));

  const nodeCenters = new Map();
  const relationXml = [];
  const boundaryXml = [];
  const personXml = [];
  const externalXml = [];

  persons.forEach((entity, index) => {
    const x = 72;
    const y = 168 + index * 132;
    drawEntityCard({ xml: personXml, entity, x, y, width: 260, height: 94, accent: '#0369a1' });
    nodeCenters.set(entity.id, { x: x + 130, y: y + 47 });
  });

  let boundaryY = 142;
  boundaries.forEach((boundary, boundaryIndex) => {
    const x = 378;
    const y = boundaryY;
    const width = 800;
    const heightForBoundary = boundaryHeights[boundaryIndex];
    boundaryXml.push(
      `<rect x="${x}" y="${y}" width="${width}" height="${heightForBoundary}" rx="22" fill="#f8fafc" stroke="#94a3b8" stroke-width="2"/>`,
      svgTextBlock(boundary.label || boundary.id, x + 28, y + 38, {
        anchor: 'start',
        fontSize: 24,
        lineHeight: 28,
        maxChars: 24,
        maxLines: 1,
        weight: '700',
        fill: '#0f172a',
      })
    );
    boundary.entities.forEach((entity, entityIndex) => {
      const column = entityIndex % 2;
      const row = Math.floor(entityIndex / 2);
      const nodeX = x + 34 + column * 380;
      const nodeY = y + 66 + row * 112;
      drawEntityCard({ xml: boundaryXml, entity, x: nodeX, y: nodeY, width: 342, height: 88, accent: '#0f766e' });
      nodeCenters.set(entity.id, { x: nodeX + 171, y: nodeY + 44 });
    });
    boundaryY += heightForBoundary + 28;
  });

  externals.forEach((entity, index) => {
    const x = 1268;
    const y = 168 + index * 126;
    drawEntityCard({ xml: externalXml, entity, x, y, width: 260, height: 92, accent: '#7c3aed' });
    nodeCenters.set(entity.id, { x: x + 130, y: y + 46 });
  });

  for (const relation of diagram.relations) {
    const from = nodeCenters.get(relation.from);
    const to = nodeCenters.get(relation.to);
    if (!from || !to) {
      continue;
    }
    const label = [relation.label, relation.technology].filter(Boolean).join(' / ');
    relationXml.push(drawRelation({ from, to, label }));
  }

  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${DEFAULT_WIDTH}" height="${height}" viewBox="0 0 ${DEFAULT_WIDTH} ${height}" role="img">`,
    `  <title>${escapeXml(caption)}</title>`,
    `  <desc>${escapeXml(sourceText)}</desc>`,
    defsXml(),
    `  <rect width="${DEFAULT_WIDTH}" height="${height}" fill="#ffffff"/>`,
    `  <rect x="32" y="34" width="${DEFAULT_WIDTH - 64}" height="${height - 68}" rx="28" fill="#f8fbff" stroke="#d8e4f0" stroke-width="2"/>`,
    svgTextBlock(diagram.title || caption, DEFAULT_WIDTH / 2, 88, {
      fontSize: 34,
      lineHeight: 38,
      maxChars: 28,
      maxLines: 1,
      weight: '700',
      fill: '#102a43',
    }),
    ...relationXml,
    ...boundaryXml,
    ...personXml,
    ...externalXml,
    '</svg>',
    '',
  ].join('\n');
}

function drawEntityCard({ xml, entity, x, y, width, height, accent }) {
  xml.push(
    `<rect x="${x}" y="${y}" width="${width}" height="${height}" rx="16" fill="#ffffff" stroke="${accent}" stroke-width="2"/>`,
    `<rect x="${x}" y="${y}" width="10" height="${height}" rx="5" fill="${accent}"/>`,
    svgTextBlock(entity.name || entity.id, x + 24, y + 32, {
      anchor: 'start',
      fontSize: 22,
      lineHeight: 26,
      maxChars: Math.max(8, Math.floor((width - 42) / 22)),
      maxLines: 1,
      weight: '700',
      fill: '#102a43',
    }),
    svgTextBlock(entity.description || entity.typeLabel || '', x + 24, y + 62, {
      anchor: 'start',
      fontSize: 16,
      lineHeight: 20,
      maxChars: Math.max(10, Math.floor((width - 42) / 16)),
      maxLines: 1,
      fill: '#475569',
    })
  );
}

function drawRelation({ from, to, label }) {
  const midX = (from.x + to.x) / 2;
  const midY = (from.y + to.y) / 2;
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const length = Math.sqrt(dx * dx + dy * dy) || 1;
  const startX = from.x + (dx / length) * 74;
  const startY = from.y + (dy / length) * 36;
  const endX = to.x - (dx / length) * 74;
  const endY = to.y - (dy / length) * 36;
  const text = String(label || '').trim();
  return [
    `<line x1="${round(startX)}" y1="${round(startY)}" x2="${round(endX)}" y2="${round(endY)}" stroke="#64748b" stroke-width="2.3" marker-end="url(#arrow-slate)"/>`,
    text
      ? svgTextBlock(text, midX, midY - 8, {
        fontSize: 15,
        lineHeight: 18,
        maxChars: 16,
        maxLines: 1,
        fill: '#334155',
      })
      : '',
  ].join('\n');
}

function parseC4Context(sourceText) {
  const diagram = { title: '', entities: [], relations: [], boundaries: [] };
  const boundaryStack = [];
  for (const rawLine of String(sourceText || '').split(/\r?\n/)) {
    const line = stripMermaidComment(rawLine).trim().replace(/;$/, '');
    if (!line || /^C4Context$/i.test(line) || /^UpdateLayoutConfig\b/i.test(line)) {
      continue;
    }
    const titleMatch = line.match(/^title\s+(.+)$/i);
    if (titleMatch) {
      diagram.title = stripMermaidQuotes(titleMatch[1]);
      continue;
    }
    const boundaryMatch = line.match(/^System_Boundary\(([\s\S]+)\)\s*\{\s*$/i);
    if (boundaryMatch) {
      const args = splitMermaidArgs(boundaryMatch[1]);
      const boundary = {
        id: args[0] || `boundary_${diagram.boundaries.length + 1}`,
        label: stripMermaidQuotes(args[1] || args[0] || `系统边界 ${diagram.boundaries.length + 1}`),
        parentId: boundaryStack.at(-1)?.id || '',
      };
      diagram.boundaries.push(boundary);
      boundaryStack.push(boundary);
      continue;
    }
    if (line === '}') {
      boundaryStack.pop();
      continue;
    }
    const entityMatch = line.match(/^(Person|System|System_Ext|Container|Container_Ext|Database|Database_Ext)\(([\s\S]+)\)$/i);
    if (entityMatch) {
      const args = splitMermaidArgs(entityMatch[2]);
      const type = entityMatch[1].toLowerCase();
      diagram.entities.push({
        id: args[0] || `entity_${diagram.entities.length + 1}`,
        name: stripMermaidQuotes(args[1] || args[0] || `节点 ${diagram.entities.length + 1}`),
        description: stripMermaidQuotes(args[2] || ''),
        type,
        typeLabel: c4TypeLabel(type),
        external: /_ext$/i.test(type),
        boundaryId: boundaryStack.at(-1)?.id || '',
      });
      continue;
    }
    const relationMatch = line.match(/^Rel\(([\s\S]+)\)$/i);
    if (relationMatch) {
      const args = splitMermaidArgs(relationMatch[1]);
      if (args[0] && args[1]) {
        diagram.relations.push({
          from: args[0],
          to: args[1],
          label: stripMermaidQuotes(args[2] || ''),
          technology: stripMermaidQuotes(args[3] || ''),
        });
      }
    }
  }
  return diagram;
}

function renderSequenceDiagramSvg({ caption, sourceText, diagram }) {
  if (diagram.participants.length === 0 || diagram.steps.length === 0) {
    throw new Error(`不支持的 Mermaid 图类型: ${caption} sequenceDiagram 未解析到参与方或消息`);
  }

  const width = Math.max(DEFAULT_WIDTH, diagram.participants.length * 230 + 180);
  const stepGap = 74;
  const height = Math.max(560, 218 + diagram.steps.length * stepGap + 90);
  const laneTop = 148;
  const laneBottom = height - 84;
  const left = 120;
  const right = width - 120;
  const gap = diagram.participants.length > 1 ? (right - left) / (diagram.participants.length - 1) : 0;
  const laneX = new Map();
  const participantXml = [];
  const stepXml = [];

  diagram.participants.forEach((participant, index) => {
    const x = diagram.participants.length > 1 ? left + gap * index : width / 2;
    laneX.set(participant.id, x);
    participantXml.push(
      `<rect x="${x - 86}" y="${laneTop}" width="172" height="54" rx="14" fill="#e0f2fe" stroke="#0369a1" stroke-width="2"/>`,
      svgTextBlock(participant.name || participant.id, x, laneTop + 34, {
        fontSize: 20,
        lineHeight: 23,
        maxChars: 10,
        maxLines: 1,
        weight: '700',
        fill: '#102a43',
      }),
      `<line x1="${x}" y1="${laneTop + 54}" x2="${x}" y2="${laneBottom}" stroke="#cbd5e1" stroke-width="2" stroke-dasharray="8 8"/>`
    );
  });

  diagram.steps.forEach((step, index) => {
    const y = laneTop + 106 + index * stepGap;
    if (step.type === 'message') {
      const fromX = laneX.get(step.from);
      const toX = laneX.get(step.to);
      if (fromX === undefined || toX === undefined) {
        return;
      }
      if (fromX === toX) {
        stepXml.push(
          `<path d="M ${fromX} ${y} h 84 v 34 h -84" fill="none" stroke="#0f766e" stroke-width="2.5" marker-end="url(#arrow-teal)"/>`,
          svgTextBlock(step.label, fromX + 98, y - 7, {
            anchor: 'start',
            fontSize: 17,
            lineHeight: 20,
            maxChars: 16,
            maxLines: 1,
            fill: '#0f172a',
          })
        );
        return;
      }
      const direction = fromX < toX ? 1 : -1;
      const startX = fromX + direction * 38;
      const endX = toX - direction * 38;
      stepXml.push(
        `<line x1="${startX}" y1="${y}" x2="${endX}" y2="${y}" stroke="#0f766e" stroke-width="2.5" marker-end="url(#arrow-teal)"/>`,
        svgTextBlock(step.label, (startX + endX) / 2, y - 9, {
          fontSize: 18,
          lineHeight: 21,
          maxChars: 22,
          maxLines: 1,
          fill: '#0f172a',
        })
      );
      return;
    }
    if (step.type === 'note') {
      const xs = step.over.map((id) => laneX.get(id)).filter((value) => value !== undefined);
      const minX = xs.length > 0 ? Math.min(...xs) - 82 : left;
      const maxX = xs.length > 0 ? Math.max(...xs) + 82 : right;
      stepXml.push(
        `<rect x="${minX}" y="${y - 30}" width="${maxX - minX}" height="52" rx="12" fill="#fff7ed" stroke="#fdba74" stroke-width="1.8"/>`,
        svgTextBlock(step.label, (minX + maxX) / 2, y + 3, {
          fontSize: 18,
          lineHeight: 21,
          maxChars: Math.max(16, Math.floor((maxX - minX) / 18)),
          maxLines: 1,
          fill: '#7c2d12',
        })
      );
      return;
    }
    if (step.type === 'control') {
      stepXml.push(
        `<rect x="${left - 44}" y="${y - 28}" width="${right - left + 88}" height="46" rx="12" fill="#f1f5f9" stroke="#94a3b8" stroke-width="1.5"/>`,
        svgTextBlock(step.label, left - 24, y + 2, {
          anchor: 'start',
          fontSize: 17,
          lineHeight: 20,
          maxChars: 36,
          maxLines: 1,
          fill: '#334155',
        })
      );
    }
  });

  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img">`,
    `  <title>${escapeXml(caption)}</title>`,
    `  <desc>${escapeXml(sourceText)}</desc>`,
    defsXml(),
    `  <rect width="${width}" height="${height}" fill="#ffffff"/>`,
    `  <rect x="32" y="34" width="${width - 64}" height="${height - 68}" rx="28" fill="#f8fbff" stroke="#d8e4f0" stroke-width="2"/>`,
    svgTextBlock(caption, width / 2, 88, {
      fontSize: 34,
      lineHeight: 38,
      maxChars: 28,
      maxLines: 1,
      weight: '700',
      fill: '#102a43',
    }),
    ...participantXml,
    ...stepXml,
    '</svg>',
    '',
  ].join('\n');
}

function parseSequenceDiagram(sourceText) {
  const participants = [];
  const participantById = new Map();
  const steps = [];

  const ensureParticipant = (id, name = id) => {
    const key = String(id || '').trim();
    if (!key || participantById.has(key)) {
      return;
    }
    const participant = { id: key, name: String(name || key).trim() };
    participantById.set(key, participant);
    participants.push(participant);
  };

  for (const rawLine of String(sourceText || '').split(/\r?\n/)) {
    const line = stripMermaidComment(rawLine).trim();
    if (!line || /^sequenceDiagram$/i.test(line) || /^autonumber\b/i.test(line)) {
      continue;
    }
    const participantMatch = line.match(/^(participant|actor)\s+([^\s]+)(?:\s+as\s+(.+))?$/i);
    if (participantMatch) {
      ensureParticipant(participantMatch[2], stripMermaidQuotes(participantMatch[3] || participantMatch[2]));
      continue;
    }
    const noteMatch = line.match(/^Note\s+(?:over|right of|left of)\s+([^:]+):\s*(.+)$/i);
    if (noteMatch) {
      const over = noteMatch[1].split(',').map((item) => item.trim()).filter(Boolean);
      over.forEach((id) => ensureParticipant(id));
      steps.push({ type: 'note', over, label: noteMatch[2].trim() });
      continue;
    }
    const messageMatch = parseSequenceMessageLine(line);
    if (messageMatch) {
      ensureParticipant(messageMatch.from);
      ensureParticipant(messageMatch.to);
      steps.push({
        type: 'message',
        from: messageMatch.from,
        to: messageMatch.to,
        label: messageMatch.label,
      });
      continue;
    }
    const controlMatch = line.match(/^(alt|else|opt|loop|par|and|critical|break)\b\s*(.*)$/i);
    if (controlMatch) {
      steps.push({ type: 'control', label: `${controlMatch[1]} ${controlMatch[2] || ''}`.trim() });
      continue;
    }
    if (/^end$/i.test(line)) {
      steps.push({ type: 'control', label: 'end' });
    }
  }

  return { participants, steps };
}

function parseSequenceMessageLine(line) {
  const match = String(line || '').match(/^(.+?)\s*(-->>|->>|-->|->|--x|-x|--o|-o|--\)|-\))\s*(.+?)\s*:\s*(.+)$/);
  if (!match) {
    return null;
  }
  return {
    from: match[1].trim(),
    to: match[3].trim(),
    label: match[4].trim(),
  };
}

function renderFlowchartSvg({ caption, sourceText, flowchart }) {
  const direction = flowchart.direction || 'TD';
  const nodeCount = flowchart.nodes.length;
  const columns = direction === 'LR'
    ? Math.min(Math.max(2, nodeCount), 5)
    : Math.min(Math.max(2, Math.ceil(Math.sqrt(nodeCount))), 4);
  const rows = Math.ceil(nodeCount / columns);
  const width = DEFAULT_WIDTH;
  const height = Math.max(430, 228 + rows * 160);
  const marginX = 90;
  const top = 168;
  const gapX = 30;
  const gapY = 60;
  const nodeWidth = Math.min(300, (width - marginX * 2 - gapX * (columns - 1)) / columns);
  const nodeHeight = 92;
  const nodeCenters = new Map();
  const nodeElements = [];

  flowchart.nodes.forEach((node, index) => {
    const row = Math.floor(index / columns);
    const column = index % columns;
    const x = marginX + column * (nodeWidth + gapX);
    const y = top + row * (nodeHeight + gapY);
    nodeCenters.set(node.id, { x: x + nodeWidth / 2, y: y + nodeHeight / 2 });
    const fill = node.shape === 'decision' ? '#fff7ed' : '#e0f2fe';
    const stroke = node.shape === 'decision' ? '#ea580c' : '#0369a1';
    if (node.shape === 'decision') {
      nodeElements.push(
        `<polygon points="${x + nodeWidth / 2},${y} ${x + nodeWidth},${y + nodeHeight / 2} ${x + nodeWidth / 2},${y + nodeHeight} ${x},${y + nodeHeight / 2}" fill="${fill}" stroke="${stroke}" stroke-width="2"/>`
      );
    } else {
      nodeElements.push(
        `<rect x="${x}" y="${y}" width="${nodeWidth}" height="${nodeHeight}" rx="16" fill="${fill}" stroke="${stroke}" stroke-width="2"/>`
      );
    }
    nodeElements.push(
      svgTextBlock(node.label, x + nodeWidth / 2, y + 37, {
        fontSize: 20,
        lineHeight: 23,
        maxChars: Math.max(9, Math.floor(nodeWidth / 20)),
        maxLines: 2,
        weight: '700',
        fill: '#102a43',
      })
    );
  });

  const edgeElements = [];
  for (const edge of flowchart.edges) {
    const from = nodeCenters.get(edge.from);
    const to = nodeCenters.get(edge.to);
    if (!from || !to) {
      continue;
    }
    const horizontal = Math.abs(from.x - to.x) >= Math.abs(from.y - to.y);
    const startX = horizontal ? from.x + Math.sign(to.x - from.x || 1) * nodeWidth / 2 : from.x;
    const startY = horizontal ? from.y : from.y + Math.sign(to.y - from.y || 1) * nodeHeight / 2;
    const endX = horizontal ? to.x - Math.sign(to.x - from.x || 1) * nodeWidth / 2 : to.x;
    const endY = horizontal ? to.y : to.y - Math.sign(to.y - from.y || 1) * nodeHeight / 2;
    edgeElements.push(
      `<line x1="${round(startX)}" y1="${round(startY)}" x2="${round(endX)}" y2="${round(endY)}" stroke="#0f766e" stroke-width="2.5" marker-end="url(#arrow-teal)"/>`
    );
    if (edge.label) {
      edgeElements.push(
        svgTextBlock(edge.label, (startX + endX) / 2, (startY + endY) / 2 - 8, {
          fontSize: 15,
          lineHeight: 18,
          maxChars: 10,
          maxLines: 1,
          fill: '#334155',
        })
      );
    }
  }

  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img">`,
    `  <title>${escapeXml(caption)}</title>`,
    `  <desc>${escapeXml(sourceText)}</desc>`,
    defsXml(),
    `  <rect width="${width}" height="${height}" fill="#ffffff"/>`,
    `  <rect x="32" y="34" width="${width - 64}" height="${height - 68}" rx="28" fill="#f8fbff" stroke="#d8e4f0" stroke-width="2"/>`,
    svgTextBlock(caption, width / 2, 88, {
      fontSize: 34,
      lineHeight: 38,
      maxChars: 28,
      maxLines: 1,
      weight: '700',
      fill: '#102a43',
    }),
    ...edgeElements,
    ...nodeElements,
    '</svg>',
    '',
  ].join('\n');
}

function parseMermaidFlowchart(sourceText) {
  const nodesById = new Map();
  const edges = [];
  let direction = 'TD';
  for (const rawLine of String(sourceText || '').split(/\r?\n/)) {
    const line = stripMermaidComment(rawLine).trim().replace(/;$/, '');
    if (!line) {
      continue;
    }
    const header = line.match(/^(flowchart|graph)\s+(TD|TB|BT|LR|RL)\b/i);
    if (header) {
      direction = header[2].toUpperCase();
      continue;
    }
    if (/^(flowchart|graph)\b/i.test(line) || /^subgraph\b/i.test(line) || /^end$/i.test(line) || /^style\b/i.test(line) || /^classDef\b/i.test(line)) {
      continue;
    }
    const edge = line.match(/^(.+?)\s*(-->|---|==>|-\.->|--o|--x)\s*(.+)$/);
    if (!edge) {
      const endpoint = parseMermaidEndpoint(line);
      if (endpoint.id) {
        upsertMermaidNode(nodesById, endpoint);
      }
      continue;
    }
    const from = parseMermaidEndpoint(edge[1]);
    const to = parseMermaidEndpoint(edge[3]);
    if (!from.id || !to.id) {
      continue;
    }
    upsertMermaidNode(nodesById, from);
    upsertMermaidNode(nodesById, to);
    edges.push({ from: from.id, to: to.id, label: extractEdgeLabel(edge[3]) });
  }

  return { direction, nodes: [...nodesById.values()], edges };
}

function renderGanttSvg({ caption, sourceText, diagram }) {
  if (diagram.tasks.length === 0) {
    throw new Error(`不支持的 Mermaid 图类型: ${caption} gantt 未解析到任务`);
  }

  const width = DEFAULT_WIDTH;
  const chartLeft = 372;
  const chartRight = width - 96;
  const chartWidth = chartRight - chartLeft;
  const top = 188;
  const rowHeight = 62;
  const height = Math.max(DEFAULT_HEIGHT, top + diagram.tasks.length * rowHeight + 136);
  const totalDays = Math.max(1, daysBetween(diagram.startDate, diagram.endDate));
  const ticks = ganttTicks(diagram.startDate, diagram.endDate, 6);
  const taskXml = [];
  const gridXml = [];
  const sectionXml = [];
  let previousSection = '';

  for (const tick of ticks) {
    const x = chartLeft + (daysBetween(diagram.startDate, tick.date) / totalDays) * chartWidth;
    gridXml.push(
      `<line x1="${round(x)}" y1="${top - 28}" x2="${round(x)}" y2="${height - 92}" stroke="#dbeafe" stroke-width="2"/>`,
      svgTextBlock(tick.label, x, top - 44, {
        fontSize: 15,
        lineHeight: 18,
        maxChars: 10,
        maxLines: 1,
        fill: '#475569',
      })
    );
  }

  diagram.tasks.forEach((task, index) => {
    const y = top + index * rowHeight;
    const centerY = y + 30;
    const startOffset = daysBetween(diagram.startDate, task.start);
    const endOffset = daysBetween(diagram.startDate, task.end);
    const x = chartLeft + (startOffset / totalDays) * chartWidth;
    const barWidth = Math.max(28, ((Math.max(endOffset, startOffset + 1) - startOffset) / totalDays) * chartWidth);
    const fill = task.status.includes('done') ? '#bfdbfe' : task.status.includes('crit') ? '#fed7aa' : '#ccfbf1';
    const stroke = task.status.includes('done') ? '#2563eb' : task.status.includes('crit') ? '#ea580c' : '#0f766e';

    if (task.section && task.section !== previousSection) {
      sectionXml.push(
        `<line x1="92" y1="${y - 8}" x2="${chartRight}" y2="${y - 8}" stroke="#e2e8f0" stroke-width="1.5"/>`,
        svgTextBlock(task.section, 104, y + 16, {
          anchor: 'start',
          fontSize: 17,
          lineHeight: 20,
          maxChars: 14,
          maxLines: 1,
          weight: '700',
          fill: '#0f766e',
        })
      );
      previousSection = task.section;
    }

    taskXml.push(
      svgTextBlock(task.name, 176, centerY + 7, {
        anchor: 'start',
        fontSize: 18,
        lineHeight: 21,
        maxChars: 15,
        maxLines: 1,
        fill: '#102a43',
      }),
      `<rect x="${round(x)}" y="${y + 12}" width="${round(barWidth)}" height="34" rx="10" fill="${fill}" stroke="${stroke}" stroke-width="2"/>`,
      svgTextBlock(task.durationLabel, x + barWidth / 2, y + 35, {
        fontSize: 14,
        lineHeight: 16,
        maxChars: 12,
        maxLines: 1,
        fill: '#0f172a',
      })
    );
  });

  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img">`,
    `  <title>${escapeXml(caption)}</title>`,
    `  <desc>${escapeXml(sourceText)}</desc>`,
    defsXml(),
    `  <rect width="${width}" height="${height}" fill="#ffffff"/>`,
    `  <rect x="32" y="34" width="${width - 64}" height="${height - 68}" rx="28" fill="#f8fbff" stroke="#d8e4f0" stroke-width="2"/>`,
    svgTextBlock(diagram.title || caption, width / 2, 88, {
      fontSize: 34,
      lineHeight: 38,
      maxChars: 28,
      maxLines: 1,
      weight: '700',
      fill: '#102a43',
    }),
    svgTextBlock(`${formatIsoDate(diagram.startDate)} 至 ${formatIsoDate(diagram.endDate)}`, width / 2, 126, {
      fontSize: 18,
      lineHeight: 21,
      maxChars: 32,
      maxLines: 1,
      fill: '#475569',
    }),
    `<line x1="${chartLeft}" y1="${top - 28}" x2="${chartRight}" y2="${top - 28}" stroke="#94a3b8" stroke-width="2"/>`,
    ...gridXml,
    ...sectionXml,
    ...taskXml,
    '</svg>',
    '',
  ].join('\n');
}

function parseGanttDiagram(sourceText) {
  const tasks = [];
  const taskById = new Map();
  const pending = [];
  let title = '';
  let section = '';
  let rollingStart = parseIsoDate('2026-01-01');

  for (const rawLine of String(sourceText || '').split(/\r?\n/)) {
    const line = stripMermaidComment(rawLine).trim();
    if (!line || /^gantt$/i.test(line) || /^(dateFormat|axisFormat|tickInterval|todayMarker|excludes|inclusiveEndDates)\b/i.test(line)) {
      continue;
    }
    const titleMatch = line.match(/^title\s+(.+)$/i);
    if (titleMatch) {
      title = normalizeMermaidLabel(stripMermaidQuotes(titleMatch[1]));
      continue;
    }
    const sectionMatch = line.match(/^section\s+(.+)$/i);
    if (sectionMatch) {
      section = normalizeMermaidLabel(stripMermaidQuotes(sectionMatch[1]));
      continue;
    }
    const taskMatch = line.match(/^(.+?)\s*:\s*(.+)$/);
    if (!taskMatch) {
      continue;
    }
    const taskName = normalizeMermaidLabel(stripMermaidQuotes(taskMatch[1]));
    const parts = taskMatch[2].split(',').map((item) => item.trim()).filter(Boolean);
    const task = parseGanttTaskParts({
      name: taskName,
      section,
      parts,
      fallbackStart: rollingStart,
      taskById,
    });
    pending.push(task);
    if (task.id) {
      taskById.set(task.id, task);
    }
    rollingStart = task.end;
  }

  for (const task of pending) {
    if (task.afterId && taskById.has(task.afterId)) {
      const afterTask = taskById.get(task.afterId);
      task.start = afterTask.end;
      task.end = addDays(task.start, task.durationDays);
    }
    task.durationLabel = `${task.durationDays} 天`;
    tasks.push(task);
  }

  const startDate = tasks.reduce((min, task) => task.start < min ? task.start : min, tasks[0]?.start || parseIsoDate('2026-01-01'));
  const endDate = tasks.reduce((max, task) => task.end > max ? task.end : max, tasks[0]?.end || addDays(startDate, 1));
  return { title, tasks, startDate, endDate };
}

function parseGanttTaskParts({ name, section, parts, fallbackStart, taskById }) {
  const statuses = [];
  let id = '';
  let start = null;
  let afterId = '';
  let durationDays = 1;
  const statusTokens = new Set(['active', 'done', 'crit', 'milestone']);

  for (const part of parts) {
    if (statusTokens.has(part.toLowerCase())) {
      statuses.push(part.toLowerCase());
      continue;
    }
    const duration = parseGanttDuration(part);
    if (duration) {
      durationDays = duration;
      continue;
    }
    const afterMatch = part.match(/^after\s+([A-Za-z0-9_.-]+)$/i);
    if (afterMatch) {
      afterId = afterMatch[1];
      start = taskById.get(afterId)?.end || null;
      continue;
    }
    const date = parseIsoDate(part);
    if (date) {
      start = date;
      continue;
    }
    if (!id && /^[A-Za-z][A-Za-z0-9_.-]*$/.test(part)) {
      id = part;
    }
  }

  const actualStart = start || fallbackStart || parseIsoDate('2026-01-01');
  return {
    id,
    name,
    section,
    status: statuses,
    afterId,
    start: actualStart,
    durationDays,
    end: addDays(actualStart, durationDays),
    durationLabel: `${durationDays} 天`,
  };
}

function parseGanttDuration(value) {
  const match = String(value || '').trim().match(/^(\d+)\s*(d|day|days|天|w|week|weeks|周)?$/i);
  if (!match) {
    return 0;
  }
  const amount = Number(match[1]);
  const unit = (match[2] || 'd').toLowerCase();
  if (unit === 'w' || unit === 'week' || unit === 'weeks' || unit === '周') {
    return amount * 7;
  }
  return amount;
}

function renderClassDiagramSvg({ caption, sourceText, diagram }) {
  if (diagram.classes.length === 0) {
    throw new Error(`不支持的 Mermaid 图类型: ${caption} classDiagram 未解析到类或对象`);
  }

  const width = DEFAULT_WIDTH;
  const columns = Math.min(3, Math.max(1, Math.ceil(Math.sqrt(diagram.classes.length))));
  const rows = Math.ceil(diagram.classes.length / columns);
  const cardWidth = 390;
  const cardHeight = 150;
  const gapX = 82;
  const gapY = 74;
  const totalWidth = columns * cardWidth + (columns - 1) * gapX;
  const left = (width - totalWidth) / 2;
  const top = 178;
  const height = Math.max(DEFAULT_HEIGHT, top + rows * cardHeight + Math.max(0, rows - 1) * gapY + 110);
  const centers = new Map();
  const relationXml = [];
  const classXml = [];

  diagram.classes.forEach((item, index) => {
    const row = Math.floor(index / columns);
    const column = index % columns;
    const x = left + column * (cardWidth + gapX);
    const y = top + row * (cardHeight + gapY);
    centers.set(item.id, { x: x + cardWidth / 2, y: y + cardHeight / 2 });
    classXml.push(
      `<rect x="${x}" y="${y}" width="${cardWidth}" height="${cardHeight}" rx="18" fill="#ffffff" stroke="#0369a1" stroke-width="2"/>`,
      `<rect x="${x}" y="${y}" width="${cardWidth}" height="48" rx="18" fill="#e0f2fe" stroke="#0369a1" stroke-width="2"/>`,
      `<line x1="${x}" y1="${y + 48}" x2="${x + cardWidth}" y2="${y + 48}" stroke="#0369a1" stroke-width="2"/>`,
      svgTextBlock(item.name, x + cardWidth / 2, y + 31, {
        fontSize: 20,
        lineHeight: 23,
        maxChars: 14,
        maxLines: 1,
        weight: '700',
        fill: '#102a43',
      }),
      ...item.members.slice(0, 4).map((member, memberIndex) => svgTextBlock(member, x + 24, y + 78 + memberIndex * 23, {
        anchor: 'start',
        fontSize: 16,
        lineHeight: 19,
        maxChars: 24,
        maxLines: 1,
        fill: '#334155',
      }))
    );
  });

  for (const relation of diagram.relations) {
    const from = centers.get(relation.from);
    const to = centers.get(relation.to);
    if (!from || !to) {
      continue;
    }
    relationXml.push(drawRelation({
      from,
      to,
      label: relation.label || relation.kind,
    }));
  }

  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img">`,
    `  <title>${escapeXml(caption)}</title>`,
    `  <desc>${escapeXml(sourceText)}</desc>`,
    defsXml(),
    `  <rect width="${width}" height="${height}" fill="#ffffff"/>`,
    `  <rect x="32" y="34" width="${width - 64}" height="${height - 68}" rx="28" fill="#f8fbff" stroke="#d8e4f0" stroke-width="2"/>`,
    svgTextBlock(caption, width / 2, 88, {
      fontSize: 34,
      lineHeight: 38,
      maxChars: 28,
      maxLines: 1,
      weight: '700',
      fill: '#102a43',
    }),
    ...relationXml,
    ...classXml,
    '</svg>',
    '',
  ].join('\n');
}

function parseClassDiagram(sourceText) {
  const classesById = new Map();
  const relations = [];
  let currentClass = null;

  const ensureClass = (id) => {
    const cleanId = normalizeMermaidLabel(stripGenericMermaidSyntax(id));
    if (!cleanId) {
      return null;
    }
    if (!classesById.has(cleanId)) {
      classesById.set(cleanId, { id: cleanId, name: cleanId, members: [] });
    }
    return classesById.get(cleanId);
  };

  for (const rawLine of String(sourceText || '').split(/\r?\n/)) {
    const line = stripMermaidComment(rawLine).trim().replace(/;$/, '');
    if (!line || /^classDiagram$/i.test(line) || /^direction\b/i.test(line)) {
      continue;
    }
    if (line === '}') {
      currentClass = null;
      continue;
    }
    const classBlock = line.match(/^class\s+([A-Za-z0-9_.\-\u4e00-\u9fa5]+)\s*\{\s*$/i);
    if (classBlock) {
      currentClass = ensureClass(classBlock[1]);
      continue;
    }
    const classSingle = line.match(/^class\s+([A-Za-z0-9_.\-\u4e00-\u9fa5]+)$/i);
    if (classSingle) {
      ensureClass(classSingle[1]);
      continue;
    }
    if (currentClass) {
      currentClass.members.push(normalizeMermaidLabel(line.replace(/^[+\-#~]\s*/, '')));
      continue;
    }
    const memberMatch = line.match(/^([A-Za-z0-9_.\-\u4e00-\u9fa5]+)\s*:\s*(.+)$/);
    if (memberMatch) {
      const item = ensureClass(memberMatch[1]);
      if (item) {
        item.members.push(normalizeMermaidLabel(memberMatch[2].replace(/^[+\-#~]\s*/, '')));
      }
      continue;
    }
    const relationMatch = line.match(/^([A-Za-z0-9_.\-\u4e00-\u9fa5]+)(?:\s+"[^"]+")?\s+([<|>*o.\-]+)\s+(?:\"[^\"]+\"\s+)?([A-Za-z0-9_.\-\u4e00-\u9fa5]+)(?:\s*:\s*(.+))?$/);
    if (relationMatch) {
      const from = relationMatch[1];
      const to = relationMatch[3];
      ensureClass(from);
      ensureClass(to);
      relations.push({
        from,
        to,
        kind: relationMatch[2],
        label: normalizeMermaidLabel(relationMatch[4] || ''),
      });
    }
  }

  return { classes: [...classesById.values()], relations };
}

function renderMindmapSvg({ caption, sourceText, diagram }) {
  if (!diagram.root) {
    throw new Error(`不支持的 Mermaid 图类型: ${caption} mindmap 未解析到根节点`);
  }

  const depth = mindmapDepth(diagram.root);
  const leaves = Math.max(1, mindmapLeafCount(diagram.root));
  const width = Math.max(DEFAULT_WIDTH, 360 + depth * 300);
  const height = Math.max(DEFAULT_HEIGHT, 210 + leaves * 88);
  const layout = [];
  const minX = 140;
  const maxX = width - 180;
  const yCursor = { value: 170 };
  layoutMindmapNode({
    node: diagram.root,
    depth: 0,
    maxDepth: Math.max(1, depth - 1),
    minX,
    maxX,
    yCursor,
    layout,
  });

  const nodeXml = [];
  const edgeXml = [];
  const byId = new Map(layout.map((item) => [item.node.id, item]));
  for (const item of layout) {
    if (item.node.parentId && byId.has(item.node.parentId)) {
      const parent = byId.get(item.node.parentId);
      edgeXml.push(
        `<path d="M ${round(parent.x + parent.width / 2)} ${round(parent.y)} C ${round((parent.x + item.x) / 2)} ${round(parent.y)}, ${round((parent.x + item.x) / 2)} ${round(item.y)}, ${round(item.x - item.width / 2)} ${round(item.y)}" fill="none" stroke="#0f766e" stroke-width="2.3" marker-end="url(#arrow-teal)"/>`
      );
    }
    const isRoot = item.node.parentId === '';
    const fill = isRoot ? '#0f766e' : '#ffffff';
    const stroke = isRoot ? '#0f766e' : '#0369a1';
    const textFill = isRoot ? '#ffffff' : '#102a43';
    nodeXml.push(
      `<rect x="${round(item.x - item.width / 2)}" y="${round(item.y - 28)}" width="${item.width}" height="56" rx="18" fill="${fill}" stroke="${stroke}" stroke-width="2"/>`,
      svgTextBlock(item.node.label, item.x, item.y + 7, {
        fontSize: isRoot ? 22 : 19,
        lineHeight: 22,
        maxChars: Math.max(8, Math.floor((item.width - 28) / 19)),
        maxLines: 1,
        weight: '700',
        fill: textFill,
      })
    );
  }

  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img">`,
    `  <title>${escapeXml(caption)}</title>`,
    `  <desc>${escapeXml(sourceText)}</desc>`,
    defsXml(),
    `  <rect width="${width}" height="${height}" fill="#ffffff"/>`,
    `  <rect x="32" y="34" width="${width - 64}" height="${height - 68}" rx="28" fill="#f8fbff" stroke="#d8e4f0" stroke-width="2"/>`,
    svgTextBlock(caption, width / 2, 88, {
      fontSize: 34,
      lineHeight: 38,
      maxChars: 28,
      maxLines: 1,
      weight: '700',
      fill: '#102a43',
    }),
    ...edgeXml,
    ...nodeXml,
    '</svg>',
    '',
  ].join('\n');
}

function parseMindmap(sourceText) {
  const stack = [];
  let root = null;
  let index = 0;

  for (const rawLine of String(sourceText || '').split(/\r?\n/)) {
    const withoutComment = stripMermaidComment(rawLine);
    const trimmed = withoutComment.trim();
    if (!trimmed || /^mindmap$/i.test(trimmed)) {
      continue;
    }
    const indent = withoutComment.match(/^\s*/)?.[0].length || 0;
    const level = Math.max(0, Math.floor(indent / 2));
    const label = normalizeMindmapLabel(trimmed);
    if (!label) {
      continue;
    }
    const node = { id: `mind-${index += 1}`, label, children: [], parentId: '' };
    while (stack.length > level) {
      stack.pop();
    }
    const parent = stack.at(-1);
    if (parent) {
      node.parentId = parent.id;
      parent.children.push(node);
    } else if (!root) {
      root = node;
    } else {
      root.children.push(node);
      node.parentId = root.id;
    }
    stack[level] = node;
  }

  return { root };
}

function renderGenericMermaidSvg({ caption, sourceText, kind }) {
  const items = genericMermaidItems(sourceText);
  if (items.length === 0) {
    throw new Error(`不支持的 Mermaid 图类型: ${kind || caption} 未解析到可展示内容`);
  }

  const columns = items.length > 8 ? 2 : 1;
  const cardWidth = columns === 2 ? 660 : 1120;
  const gapX = 44;
  const gapY = 24;
  const rowHeight = 96;
  const rows = Math.ceil(items.length / columns);
  const width = DEFAULT_WIDTH;
  const height = Math.max(DEFAULT_HEIGHT, 220 + rows * (rowHeight + gapY) + 80);
  const left = (width - (columns * cardWidth + (columns - 1) * gapX)) / 2;
  const top = 178;
  const cards = [];

  items.forEach((item, index) => {
    const row = Math.floor(index / columns);
    const column = index % columns;
    const x = left + column * (cardWidth + gapX);
    const y = top + row * (rowHeight + gapY);
    cards.push(
      `<rect x="${x}" y="${y}" width="${cardWidth}" height="${rowHeight}" rx="16" fill="#ffffff" stroke="#0f766e" stroke-width="2"/>`,
      `<circle cx="${x + 38}" cy="${y + 48}" r="18" fill="#ccfbf1" stroke="#0f766e" stroke-width="2"/>`,
      svgTextBlock(String(index + 1), x + 38, y + 55, {
        fontSize: 18,
        lineHeight: 20,
        maxChars: 2,
        maxLines: 1,
        weight: '700',
        fill: '#0f766e',
      }),
      svgTextBlock(item.title, x + 72, y + 38, {
        anchor: 'start',
        fontSize: 22,
        lineHeight: 25,
        maxChars: Math.max(12, Math.floor((cardWidth - 104) / 22)),
        maxLines: 1,
        weight: '700',
        fill: '#102a43',
      }),
      item.detail
        ? svgTextBlock(item.detail, x + 72, y + 68, {
          anchor: 'start',
          fontSize: 16,
          lineHeight: 20,
          maxChars: Math.max(16, Math.floor((cardWidth - 104) / 16)),
          maxLines: 1,
          fill: '#475569',
        })
        : ''
    );
  });

  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img">`,
    `  <title>${escapeXml(caption)}</title>`,
    `  <desc>${escapeXml(sourceText)}</desc>`,
    defsXml(),
    `  <rect width="${width}" height="${height}" fill="#ffffff"/>`,
    `  <rect x="32" y="34" width="${width - 64}" height="${height - 68}" rx="28" fill="#f8fbff" stroke="#d8e4f0" stroke-width="2"/>`,
    svgTextBlock(caption, width / 2, 88, {
      fontSize: 34,
      lineHeight: 38,
      maxChars: 28,
      maxLines: 1,
      weight: '700',
      fill: '#102a43',
    }),
    svgTextBlock(`通用图示渲染：${kind || 'mermaid'}`, width / 2, 132, {
      fontSize: 20,
      lineHeight: 24,
      maxChars: 32,
      maxLines: 1,
      fill: '#475569',
    }),
    ...cards,
    '</svg>',
    '',
  ].join('\n');
}

function genericMermaidItems(sourceText) {
  const items = [];
  for (const rawLine of String(sourceText || '').split(/\r?\n/)) {
    const line = stripMermaidComment(rawLine).trim().replace(/;$/, '');
    if (!line || genericSkipLine(line)) {
      continue;
    }
    const item = genericMermaidItem(line);
    if (item.title && !items.some((existing) => existing.title === item.title && existing.detail === item.detail)) {
      items.push(item);
    }
    if (items.length >= 18) {
      break;
    }
  }
  return items;
}

function genericSkipLine(line) {
  return /^(C4Context|sequenceDiagram|flowchart|graph|mindmap|classDiagram|erDiagram|stateDiagram(?:-v2)?|journey|timeline|gantt|pie|requirementDiagram|gitGraph|quadrantChart)\b/i.test(line)
    || /^(direction|dateFormat|axisFormat|tickInterval|todayMarker|classDef|style|linkStyle|accTitle|accDescr)\b/i.test(line)
    || /^end$/i.test(line)
    || /^[{}]$/.test(line);
}

function genericMermaidItem(line) {
  const title = line.match(/^title\s+(.+)$/i);
  if (title) {
    return { title: normalizeMermaidLabel(stripMermaidQuotes(title[1])), detail: '标题' };
  }

  const section = line.match(/^section\s+(.+)$/i);
  if (section) {
    return { title: normalizeMermaidLabel(stripMermaidQuotes(section[1])), detail: '分组' };
  }

  const participant = line.match(/^(participant|actor)\s+([^\s]+)(?:\s+as\s+(.+))?$/i);
  if (participant) {
    return { title: normalizeMermaidLabel(stripMermaidQuotes(participant[3] || participant[2])), detail: '参与方' };
  }

  const classDecl = line.match(/^class\s+([A-Za-z0-9_.\-\u4e00-\u9fa5]+)(?:\s*\{)?$/i);
  if (classDecl) {
    return { title: classDecl[1], detail: '类/对象' };
  }

  const classMember = line.match(/^([A-Za-z0-9_.\-\u4e00-\u9fa5]+)\s*:\s*(.+)$/);
  if (classMember) {
    return { title: classMember[1], detail: normalizeMermaidLabel(classMember[2]) };
  }

  const erRelation = line.match(/^([A-Za-z0-9_.\-\u4e00-\u9fa5]+)\s+\S+\s+([A-Za-z0-9_.\-\u4e00-\u9fa5]+)\s*:\s*(.+)$/);
  if (erRelation) {
    return {
      title: `${erRelation[1]} -> ${erRelation[2]}`,
      detail: normalizeMermaidLabel(erRelation[3]),
    };
  }

  const relationship = line.match(/^(.+?)\s*(?:-->|---|==>|-\.-|--|->|<\|--|\*--|o--)\s*(.+)$/);
  if (relationship) {
    const left = normalizeMermaidLabel(stripGenericMermaidSyntax(relationship[1]));
    const right = normalizeMermaidLabel(stripGenericMermaidSyntax(relationship[2]));
    return { title: [left, right].filter(Boolean).join(' -> '), detail: '关系' };
  }

  const pieItem = line.match(/^["']?(.+?)["']?\s*:\s*(.+)$/);
  if (pieItem) {
    return { title: normalizeMermaidLabel(stripMermaidQuotes(pieItem[1])), detail: normalizeMermaidLabel(pieItem[2]) };
  }

  const cleaned = normalizeMermaidLabel(stripGenericMermaidSyntax(line));
  return { title: cleaned, detail: '' };
}

function stripGenericMermaidSyntax(value) {
  return String(value || '')
    .replace(/^\s*[-*+]\s*/, '')
    .replace(/^\s*[A-Za-z0-9_.-]+\s*:/, '')
    .replace(/[\[\](){}<>]/g, ' ')
    .replace(/["']/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function parseIsoDate(value) {
  const match = String(value || '').trim().match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) {
    return null;
  }
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return Number.isNaN(date.getTime()) ? null : date;
}

function addDays(date, days) {
  const base = date instanceof Date && !Number.isNaN(date.getTime()) ? date : parseIsoDate('2026-01-01');
  const next = new Date(base.getTime());
  next.setUTCDate(next.getUTCDate() + Number(days || 0));
  return next;
}

function daysBetween(start, end) {
  const day = 24 * 60 * 60 * 1000;
  return Math.round(((end?.getTime?.() || 0) - (start?.getTime?.() || 0)) / day);
}

function formatIsoDate(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    return '';
  }
  return date.toISOString().slice(0, 10);
}

function ganttTicks(startDate, endDate, count) {
  const totalDays = Math.max(1, daysBetween(startDate, endDate));
  const ticks = [];
  for (let index = 0; index < count; index += 1) {
    const offset = Math.round((totalDays / Math.max(1, count - 1)) * index);
    const date = addDays(startDate, offset);
    ticks.push({ date, label: formatIsoDate(date).slice(5) });
  }
  return ticks;
}

function normalizeMindmapLabel(value) {
  return normalizeMermaidLabel(
    stripMermaidQuotes(String(value || '')
      .replace(/^root\s*/i, '')
      .replace(/^\(+|\)+$/g, '')
      .replace(/^\[+|\]+$/g, '')
      .replace(/^\{+|\}+$/g, '')
      .replace(/^\(+|\)+$/g, '')
      .trim())
  );
}

function mindmapDepth(node) {
  if (!node || !node.children || node.children.length === 0) {
    return 1;
  }
  return 1 + Math.max(...node.children.map((child) => mindmapDepth(child)));
}

function mindmapLeafCount(node) {
  if (!node || !node.children || node.children.length === 0) {
    return 1;
  }
  return node.children.reduce((sum, child) => sum + mindmapLeafCount(child), 0);
}

function layoutMindmapNode({ node, depth, maxDepth, minX, maxX, yCursor, layout }) {
  const x = minX + ((maxX - minX) * depth) / Math.max(1, maxDepth);
  const width = depth === 0 ? 240 : 220;
  if (!node.children || node.children.length === 0) {
    const y = yCursor.value;
    yCursor.value += 88;
    layout.push({ node, x, y, width });
    return y;
  }

  const childYs = node.children.map((child) => layoutMindmapNode({
    node: child,
    depth: depth + 1,
    maxDepth,
    minX,
    maxX,
    yCursor,
    layout,
  }));
  const y = childYs.reduce((sum, value) => sum + value, 0) / childYs.length;
  layout.push({ node, x, y, width });
  return y;
}

function parseMermaidEndpoint(value) {
  const endpoint = String(value || '')
    .trim()
    .replace(/^\|[^|]*\|/, '')
    .replace(/\|[^|]*\|$/, '')
    .trim();
  const match = endpoint.match(/^([A-Za-z0-9_.\-\u4e00-\u9fa5]+)\s*(?:\["([^"]+)"\]|\['([^']+)'\]|\[([^\]]+)\]|\("([^"]+)"\)|\('([^']+)'\)|\(([^)]+)\)|\{"([^"]+)"\}|\{'([^']+)'\}|\{([^}]+)\})?$/);
  if (!match) {
    return { id: '', label: '', shape: 'process' };
  }
  const label = match.slice(2).find((item) => item !== undefined) || match[1];
  const shape = match[8] || match[9] || match[10] ? 'decision' : 'process';
  return { id: match[1], label: normalizeMermaidLabel(label), shape };
}

function extractEdgeLabel(value) {
  const match = String(value || '').trim().match(/^\|([^|]+)\|/);
  return match ? normalizeMermaidLabel(match[1]) : '';
}

function upsertMermaidNode(nodesById, node) {
  const existing = nodesById.get(node.id);
  if (!existing) {
    nodesById.set(node.id, node);
    return;
  }
  if (existing.label === existing.id && node.label !== node.id) {
    existing.label = node.label;
  }
  if (existing.shape !== 'decision' && node.shape === 'decision') {
    existing.shape = 'decision';
  }
}

function normalizeMermaidLabel(value) {
  return String(value || '')
    .replace(/<br\s*\/?>/gi, ' ')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function splitMermaidArgs(value) {
  const args = [];
  let current = '';
  let quote = '';
  let depth = 0;
  for (const char of String(value || '')) {
    if (quote) {
      current += char;
      if (char === quote) {
        quote = '';
      }
      continue;
    }
    if (char === '"' || char === "'") {
      quote = char;
      current += char;
      continue;
    }
    if (char === '(' || char === '[' || char === '{') {
      depth += 1;
      current += char;
      continue;
    }
    if (char === ')' || char === ']' || char === '}') {
      depth = Math.max(0, depth - 1);
      current += char;
      continue;
    }
    if (char === ',' && depth === 0) {
      args.push(stripMermaidQuotes(current.trim()));
      current = '';
      continue;
    }
    current += char;
  }
  if (current.trim()) {
    args.push(stripMermaidQuotes(current.trim()));
  }
  return args;
}

function stripMermaidQuotes(value) {
  return String(value || '')
    .trim()
    .replace(/^["']|["']$/g, '')
    .replace(/\\"/g, '"')
    .replace(/\\'/g, "'");
}

function stripMermaidComment(value) {
  return String(value || '').replace(/%%.*$/, '');
}

function c4TypeLabel(type) {
  if (/person/i.test(type)) return '使用者';
  if (/database/i.test(type)) return '数据库';
  if (/container/i.test(type)) return '容器';
  return /_ext$/i.test(type) ? '外部系统' : '系统';
}

function defsXml() {
  return [
    '  <defs>',
    '    <marker id="arrow-teal" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto" markerUnits="strokeWidth">',
    '      <path d="M2,2 L10,6 L2,10 Z" fill="#0f766e"/>',
    '    </marker>',
    '    <marker id="arrow-slate" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto" markerUnits="strokeWidth">',
    '      <path d="M2,2 L10,6 L2,10 Z" fill="#64748b"/>',
    '    </marker>',
    '  </defs>',
  ].join('\n');
}

function svgTextBlock(text, x, y, options = {}) {
  const lines = wrapSvgText(text, options.maxChars || 16, options.maxLines || 2);
  const lineHeight = options.lineHeight || 24;
  const fontSize = options.fontSize || 22;
  const weight = options.weight ? ` font-weight="${options.weight}"` : '';
  const fill = options.fill || '#1d3557';
  const anchor = options.anchor || 'middle';
  return [
    `<text x="${round(x)}" y="${round(y)}" text-anchor="${anchor}" font-family="${FONT_STACK}" font-size="${fontSize}" fill="${fill}"${weight}>`,
    ...lines.map((line, index) => `<tspan x="${round(x)}" dy="${index === 0 ? 0 : lineHeight}">${escapeXml(line)}</tspan>`),
    '</text>',
  ].join('');
}

function wrapSvgText(text, maxCharsPerLine = 18, maxLines = 3) {
  const chars = Array.from(String(text || '').trim());
  if (chars.length === 0) {
    return [''];
  }
  const lines = [];
  for (let index = 0; index < chars.length && lines.length < maxLines; index += maxCharsPerLine) {
    lines.push(chars.slice(index, index + maxCharsPerLine).join(''));
  }
  if (chars.length > maxCharsPerLine * maxLines) {
    lines[maxLines - 1] = `${Array.from(lines[maxLines - 1]).slice(0, Math.max(1, maxCharsPerLine - 1)).join('')}...`;
  }
  return lines;
}

function firstMeaningfulLine(sourceText) {
  return String(sourceText || '')
    .split(/\r?\n/)
    .map((line) => stripMermaidComment(line).trim())
    .find(Boolean) || '';
}

function round(value) {
  return Math.round(Number(value) * 10) / 10;
}

function escapeXml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

module.exports = {
  renderDeterministicMermaidSvg,
  parseC4Context,
  parseMermaidFlowchart,
  parseSequenceDiagram,
};
