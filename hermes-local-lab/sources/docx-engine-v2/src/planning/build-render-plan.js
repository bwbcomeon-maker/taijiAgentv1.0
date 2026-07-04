const { validateDomainObject } = require('../domain/validate');

function buildRenderPlan({ sourcePackage, templatePackage, assetPackage } = {}) {
  if (!sourcePackage) {
    throw new Error('sourcePackage is required.');
  }
  if (!templatePackage) {
    throw new Error('templatePackage is required.');
  }
  if (!assetPackage) {
    throw new Error('assetPackage is required.');
  }

  const sectionById = new Map((sourcePackage.sections || []).map((section) => [section.sectionId, section]));
  const renderPlan = {
    schemaVersion: 'docx-engine-v2/render-plan',
    jobId: sourcePackage.sourceRef?.sha256
      ? `job-${sourcePackage.sourceRef.sha256.slice(0, 12)}`
      : 'job-render-plan',
    templateId: templatePackage.templateId || templatePackage.id,
    sections: (sourcePackage.sections || []).map((section) => ({
      ...section,
      blockIds: [...(section.blockIds || [])],
      metadata: { ...(section.metadata || {}) },
    })),
    tables: (assetPackage.tables || []).map((table, index) => ({
      tableId: table.tableId,
      title: table.title || `表格 ${index + 1}`,
      sectionId: table.sectionId || '',
      afterBlockId: table.afterBlockId || findPlacementBlockId(sourcePackage, table),
      anchorText: table.anchorText || table.title || table.tableId,
      metadata: {
        ...(table.metadata || {}),
        sectionTitle: sectionById.get(table.sectionId)?.title || '',
        templatePath: `tables.${index}`,
      },
    })),
    figures: (assetPackage.figures || []).map((figure, index) => {
      const sectionTitle = sectionById.get(figure.sectionId)?.title || '';

      return {
        figureId: figure.figureId,
        caption: figure.caption || `图 ${index + 1}`,
        sectionId: figure.sectionId || '',
        sectionTitle,
        afterBlockId: findPlacementBlockId(sourcePackage, figure),
        anchorText: figure.anchorText || figure.caption || figure.figureId,
        displayPath: figure.displayPath,
        metadata: {
          ...(figure.metadata || {}),
          sectionTitle,
          templatePath: `images.${index}`,
        },
      };
    }),
    templateData: {
      title: sourcePackage.title || '',
      sections: buildTemplateSections(sourcePackage, assetPackage),
      images: (assetPackage.figures || []).map((figure, index) => ({
        figureId: figure.figureId,
        path: figure.displayPath,
        caption: figure.caption || `图 ${index + 1}`,
        metadata: {
          ...(figure.metadata || {}),
          sectionId: figure.sectionId || '',
          sectionTitle: sectionById.get(figure.sectionId)?.title || '',
          templatePath: `images.${index}`,
        },
      })),
      tables: (assetPackage.tables || []).map((table, index) => ({
        tableId: table.tableId,
        title: table.title || `表格 ${index + 1}`,
        rows: rowsToTemplateObjects(table.headers || [], table.rows || []),
        metadata: {
          ...(table.metadata || {}),
          sectionId: table.sectionId || '',
          sectionTitle: sectionById.get(table.sectionId)?.title || '',
          templatePath: `tables.${index}`,
        },
      })),
      metadata: {
        templateId: templatePackage.templateId || templatePackage.id,
        assetDir: assetPackage.assetDir,
      },
    },
    warnings: [],
  };

  assertValidDomainObject('RenderPlan', renderPlan);
  return renderPlan;
}

function buildTemplateSections(sourcePackage, assetPackage) {
  const tableIds = new Set((assetPackage.tables || []).map((table) => table.tableId));
  const figureIds = new Set((assetPackage.figures || []).map((figure) => figure.figureId));

  return (sourcePackage.sections || []).map((section) => ({
    sectionId: section.sectionId,
    title: section.title,
    level: section.level,
    blocks: (sourcePackage.blocks || [])
      .filter((block) => block.sectionId === section.sectionId)
      .map((block) => toTemplateBlock(block, tableIds, figureIds))
      .filter(Boolean),
  }));
}

function toTemplateBlock(block, tableIds, figureIds) {
  const tableId = block.metadata?.tableId;
  if (tableId && tableIds.has(tableId)) {
    return {
      type: 'table',
      blockId: block.id,
      tableId,
      anchor: block.anchorText || tableId,
    };
  }

  const figureId = block.metadata?.figureId;
  if (figureId && figureIds.has(figureId)) {
    return {
      type: 'figure',
      blockId: block.id,
      figureId,
      anchor: block.anchorText || figureId,
    };
  }

  if (block.type === 'paragraph' || block.type === 'heading') {
    return {
      type: 'paragraph',
      blockId: block.id,
      text: block.text || '',
    };
  }

  return null;
}

function findPlacementBlockId(sourcePackage, item) {
  if (item.afterBlockId) {
    return item.afterBlockId;
  }

  const block = (sourcePackage.blocks || []).find(
    (candidate) =>
      candidate.metadata?.figureId === item.figureId || candidate.metadata?.tableId === item.tableId
  );
  if (!block) {
    return '';
  }

  const blockIndex = sourcePackage.blocks.indexOf(block);
  for (let index = blockIndex - 1; index >= 0; index -= 1) {
    const previous = sourcePackage.blocks[index];
    if (previous.sectionId === block.sectionId) {
      return previous.id;
    }
  }

  return block.id;
}

function rowsToTemplateObjects(headers, rows) {
  if (!headers.length) {
    return rows;
  }

  return rows.map((row) => {
    if (!Array.isArray(row)) {
      return row;
    }

    return Object.fromEntries(headers.map((header, index) => [header || `c${index + 1}`, row[index] ?? '']));
  });
}

function assertValidDomainObject(schemaName, value) {
  const result = validateDomainObject(schemaName, value);
  if (!result.ok) {
    throw new Error(`${schemaName} validation failed: ${JSON.stringify(result.errors)}`);
  }
}

module.exports = { buildRenderPlan };
