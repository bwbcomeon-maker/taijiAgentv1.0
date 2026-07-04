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
  const templateImageBindings = buildTemplateImages(sourcePackage, assetPackage, sectionById);
  const templateImages = templateImageBindings.images;
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
      sections: buildTemplateSections(sourcePackage, assetPackage, templateImageBindings.figureIdBySourceImageId),
      images: templateImages,
      tables: (assetPackage.tables || []).map((table, index) => ({
        tableId: table.tableId,
        title: table.title || `表格 ${index + 1}`,
        headers: headersToTemplateObject(table.headers || []),
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

function buildTemplateSections(sourcePackage, assetPackage, figureIdBySourceImageId = new Map()) {
  const tableIds = new Set((assetPackage.tables || []).map((table) => table.tableId));
  const figureIds = new Set((assetPackage.figures || []).map((figure) => figure.figureId));
  const imageIds = new Set((assetPackage.images || []).map((image) => image.imageId));

  return (sourcePackage.sections || []).map((section) => ({
    sectionId: section.sectionId,
    title: section.title,
    level: section.level,
    blocks: (sourcePackage.blocks || [])
      .filter((block) => block.sectionId === section.sectionId)
      .map((block) => toTemplateBlock(block, tableIds, figureIds, imageIds, figureIdBySourceImageId))
      .filter(Boolean),
  }));
}

function buildTemplateImages(sourcePackage, assetPackage, sectionById) {
  const figureById = new Map((assetPackage.figures || []).map((figure) => [figure.figureId, figure]));
  const imageById = new Map((assetPackage.images || []).map((image) => [image.imageId, image]));
  const usedImageKeys = new Set();
  const figureIdBySourceImageId = new Map();
  const allocateFigureId = createFigureIdAllocator(new Set((assetPackage.figures || []).map((figure) => figure.figureId)));
  const orderedImages = [];

  for (const block of sourcePackage.blocks || []) {
    const figureId = block.metadata?.figureId;
    if (figureId && figureById.has(figureId) && !usedImageKeys.has(`figure:${figureId}`)) {
      usedImageKeys.add(`figure:${figureId}`);
      orderedImages.push(toTemplateFigureImage(figureById.get(figureId), orderedImages.length, sectionById));
      continue;
    }

    const imageId = block.metadata?.imageId;
    if (imageId && imageById.has(imageId) && !usedImageKeys.has(`image:${imageId}`)) {
      usedImageKeys.add(`image:${imageId}`);
      orderedImages.push(
        toTemplateMarkdownImage(
          imageById.get(imageId),
          orderedImages.length,
          sectionById,
          figureIdBySourceImageId,
          allocateFigureId
        )
      );
    }
  }

  for (const figure of assetPackage.figures || []) {
    if (!usedImageKeys.has(`figure:${figure.figureId}`)) {
      usedImageKeys.add(`figure:${figure.figureId}`);
      orderedImages.push(toTemplateFigureImage(figure, orderedImages.length, sectionById));
    }
  }

  for (const image of assetPackage.images || []) {
    if (!usedImageKeys.has(`image:${image.imageId}`)) {
      usedImageKeys.add(`image:${image.imageId}`);
      orderedImages.push(
        toTemplateMarkdownImage(
          image,
          orderedImages.length,
          sectionById,
          figureIdBySourceImageId,
          allocateFigureId
        )
      );
    }
  }

  return { images: orderedImages, figureIdBySourceImageId };
}

function toTemplateFigureImage(figure, index, sectionById) {
  return {
    figureId: figure.figureId,
    path: figure.displayPath,
    caption: figure.caption || `图 ${index + 1}`,
    metadata: {
      ...(figure.metadata || {}),
      sourceType: 'figure',
      sectionId: figure.sectionId || '',
      sectionTitle: sectionById.get(figure.sectionId)?.title || '',
      templatePath: `images.${index}`,
    },
  };
}

function toTemplateMarkdownImage(image, index, sectionById, figureIdBySourceImageId, allocateFigureId) {
  const figureId = figureIdBySourceImageId.get(image.imageId) || allocateFigureId();
  figureIdBySourceImageId.set(image.imageId, figureId);

  return {
    figureId,
    path: image.displayPath,
    caption: image.caption || `图片 ${index + 1}`,
    metadata: {
      ...(image.metadata || {}),
      imageId: image.imageId,
      sourceImageId: image.imageId,
      sourceType: 'image',
      sourcePath: image.sourcePath,
      sectionId: image.sectionId || '',
      sectionTitle: sectionById.get(image.sectionId)?.title || '',
      templatePath: `images.${index}`,
    },
  };
}

function toTemplateBlock(block, tableIds, figureIds, imageIds, figureIdBySourceImageId) {
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

  const imageId = block.metadata?.imageId;
  if (imageId && imageIds.has(imageId)) {
    return {
      type: 'figure',
      blockId: block.id,
      figureId: figureIdBySourceImageId.get(imageId) || imageId,
      sourceImageId: imageId,
      title: block.caption || block.text || imageId,
      anchor: block.anchorText || imageId,
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
  const keys = headers.map((_, index) => `c${index + 1}`);

  return rows.map((row) => {
    if (Array.isArray(row)) {
      return Object.fromEntries(keys.map((key, index) => [key, row[index] ?? '']));
    }
    if (row && typeof row === 'object') {
      return Object.fromEntries(
        keys.map((key, index) => [key, row[key] ?? row[headers[index]] ?? ''])
      );
    }

    return { c1: String(row ?? '') };
  });
}

function headersToTemplateObject(headers) {
  return Object.fromEntries((headers || []).map((header, index) => [`c${index + 1}`, header || `列${index + 1}`]));
}

function createFigureIdAllocator(usedIds) {
  let nextIndex = 1;

  return () => {
    let figureId = nextFigureId(nextIndex);
    while (usedIds.has(figureId)) {
      nextIndex += 1;
      figureId = nextFigureId(nextIndex);
    }

    usedIds.add(figureId);
    nextIndex += 1;
    return figureId;
  };
}

function nextFigureId(index) {
  return `fig-${String(index).padStart(3, '0')}`;
}

function assertValidDomainObject(schemaName, value) {
  const result = validateDomainObject(schemaName, value);
  if (!result.ok) {
    throw new Error(`${schemaName} validation failed: ${JSON.stringify(result.errors)}`);
  }
}

module.exports = { buildRenderPlan };
