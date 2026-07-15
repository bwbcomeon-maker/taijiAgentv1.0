const { validateDomainObject } = require('../domain/validate');

function buildRenderPlan({ sourcePackage, templatePackage, assetPackage, documentMetadata, canonicalBinding, rendererIdentity, renderInputBinding, renderInputFingerprint, assetManifestPath = '' } = {}) {
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
    ...(documentMetadata ? { documentMetadata } : {}),
    ...(canonicalBinding ? { canonicalBinding } : {}),
    ...(rendererIdentity ? { rendererIdentity } : {}),
    ...(renderInputBinding ? { renderInputBinding } : {}),
    ...(renderInputFingerprint ? { renderInputFingerprint } : {}),
    ...(assetManifestPath ? { assetManifestPath } : {}),
    sections: (sourcePackage.sections || []).map((section) => ({
      ...section,
      blockIds: [...(section.blockIds || [])],
      metadata: { ...(section.metadata || {}) },
    })),
    tables: (assetPackage.tables || []).map((table, index) => {
      const sectionTitle = sectionById.get(table.sectionId)?.title || '';
      const title = readableTableTitle(table, index, sectionTitle);
      return {
        tableId: table.tableId,
        title,
        sectionId: table.sectionId || '',
        afterBlockId: table.afterBlockId || findPlacementBlockId(sourcePackage, table),
        anchorText: table.anchorText || title || table.tableId,
        metadata: {
          ...(table.metadata || {}),
          sectionTitle,
          templatePath: `tables.${index}`,
        },
      };
    }),
    figures: (assetPackage.figures || []).map((figure, index) => {
      const sectionTitle = sectionById.get(figure.sectionId)?.title || '';

      return {
        figureId: figure.figureId,
        ...(figure.logicalAssetId ? { logicalAssetId: figure.logicalAssetId } : {}),
        ...(figure.occurrenceId ? { occurrenceId: figure.occurrenceId } : {}),
        caption: readableFigureCaption(figure, index, sectionTitle),
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
      title: documentMetadata?.title || sourcePackage.title || '',
      sections: buildTemplateSections(sourcePackage, assetPackage, templateImageBindings.figureIdBySourceImageId),
      images: templateImages,
      tables: (assetPackage.tables || []).map((table, index) => {
        const sectionTitle = sectionById.get(table.sectionId)?.title || '';
        return {
          tableId: table.tableId,
          title: readableTableTitle(table, index, sectionTitle),
          headers: headersToTemplateObject(table.headers || []),
          columns: columnsToTemplateObjects(table.headers || []),
          rows: rowsToTemplateObjects(table.headers || [], table.rows || []),
          metadata: {
            ...(table.metadata || {}),
            sectionId: table.sectionId || '',
            sectionTitle,
            templatePath: `tables.${index}`,
          },
        };
      }),
      metadata: {
        templateId: templatePackage.templateId || templatePackage.id,
        assetDir: assetPackage.assetDir,
        ...(documentMetadata ? { documentMetadata } : {}),
        ...(canonicalBinding ? { canonicalBinding } : {}),
        ...(rendererIdentity ? { rendererIdentity } : {}),
        ...(renderInputFingerprint ? { renderInputFingerprint } : {}),
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
    title: cleanSectionTitle(section.title) || section.title,
    level: section.level,
    metadata: { originalTitle: section.title || '' },
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
      orderedImages.push(toTemplateFigureImage(
        figureById.get(figureId),
        orderedImages.length,
        sectionById,
        placementMetadata(sourcePackage, figureById.get(figureId), block)
      ));
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
          allocateFigureId,
          placementMetadata(sourcePackage, imageById.get(imageId), block)
        )
      );
    }
  }

  for (const figure of assetPackage.figures || []) {
    if (!usedImageKeys.has(`figure:${figure.figureId}`)) {
      usedImageKeys.add(`figure:${figure.figureId}`);
      orderedImages.push(toTemplateFigureImage(
        figure,
        orderedImages.length,
        sectionById,
        placementMetadata(sourcePackage, figure)
      ));
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
          allocateFigureId,
          placementMetadata(sourcePackage, image)
        )
      );
    }
  }

  return { images: orderedImages, figureIdBySourceImageId };
}

function toTemplateFigureImage(figure, index, sectionById, placement = {}) {
  return {
    figureId: figure.figureId,
    ...(figure.logicalAssetId ? { logicalAssetId: figure.logicalAssetId } : {}),
    ...(figure.occurrenceId ? { occurrenceId: figure.occurrenceId } : {}),
    path: figure.displayPath,
    sha256: figure.sha256,
    caption: readableFigureCaption(figure, index, sectionById.get(figure.sectionId)?.title || ''),
    dimensions: figure.dimensions || {},
    layoutIntent: inferFigureLayoutIntent(figure),
    metadata: {
      ...(figure.metadata || {}),
      sourceType: 'figure',
      sectionId: figure.sectionId || '',
      sectionTitle: sectionById.get(figure.sectionId)?.title || '',
      blockId: placement.blockId || '',
      afterBlockId: placement.afterBlockId || '',
      anchorText: placement.anchorText || figure.anchorText || '',
      templatePath: `images.${index}`,
    },
  };
}

function toTemplateMarkdownImage(
  image,
  index,
  sectionById,
  figureIdBySourceImageId,
  allocateFigureId,
  placement = {}
) {
  const figureId = figureIdBySourceImageId.get(image.imageId) || allocateFigureId();
  figureIdBySourceImageId.set(image.imageId, figureId);

  return {
    figureId,
    ...(image.logicalAssetId ? { logicalAssetId: image.logicalAssetId } : {}),
    ...(image.occurrenceId ? { occurrenceId: image.occurrenceId } : {}),
    path: image.displayPath,
    sha256: image.sha256,
    caption: readableFigureCaption(image, index, sectionById.get(image.sectionId)?.title || ''),
    dimensions: image.dimensions || {},
    layoutIntent: inferFigureLayoutIntent(image),
    metadata: {
      ...(image.metadata || {}),
      imageId: image.imageId,
      sourceImageId: image.imageId,
      sourceType: 'image',
      sourcePath: image.sourcePath,
      sectionId: image.sectionId || '',
      sectionTitle: sectionById.get(image.sectionId)?.title || '',
      blockId: placement.blockId || '',
      afterBlockId: placement.afterBlockId || '',
      anchorText: placement.anchorText || image.anchorText || '',
      templatePath: `images.${index}`,
    },
  };
}

function inferFigureLayoutIntent(item = {}) {
  const text = `${item.caption || ''} ${item.anchorText || ''} ${item.sourceType || ''}`.toLowerCase();
  if (/gantt|甘特/.test(text)) {
    return 'gantt';
  }
  if (/topo|network|网络|拓扑|vlan|交换机/.test(text)) {
    return 'network';
  }
  if (/flowchart|流程|时序|sequence/.test(text)) {
    return 'flowchart';
  }
  if (/架构|architecture|c4context/.test(text)) {
    return 'architecture';
  }
  return 'normal';
}

function placementMetadata(sourcePackage, item, block = null) {
  return {
    blockId: block?.id || '',
    afterBlockId: findPlacementBlockId(sourcePackage, item),
    anchorText: block?.anchorText || item?.anchorText || '',
  };
}

function toTemplateBlock(block, tableIds, figureIds, imageIds, figureIdBySourceImageId) {
  if (block.type === 'figure-derivative') {
    return null;
  }
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

  if (block.type === 'paragraph') {
    return {
      type: 'paragraph',
      blockId: block.id,
      text: block.text || '',
    };
  }

  if (block.type === 'heading') {
    return {
      type: 'heading',
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
      (item.figureId && candidate.metadata?.figureId === item.figureId) ||
      (item.tableId && candidate.metadata?.tableId === item.tableId) ||
      (item.imageId && candidate.metadata?.imageId === item.imageId)
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
    return (rows || []).map((row) => {
      if (Array.isArray(row)) {
        return rowToTemplateObject([], row);
      }
      if (row && typeof row === 'object') {
        const keys = Object.keys(row).filter((key) => /^c\d+$/.test(key));
        const values = keys
          .sort((left, right) => Number(left.slice(1)) - Number(right.slice(1)))
          .map((key) => row[key]);
        return {
          ...row,
          cells: values.map((value, index) => ({ key: `c${index + 1}`, text: value ?? '' })),
        };
      }
      return { c1: String(row ?? ''), cells: [{ key: 'c1', text: String(row ?? '') }] };
    });
  }
  const keys = headers.map((_, index) => `c${index + 1}`);

  return rows.map((row) => {
    if (Array.isArray(row)) {
      return rowToTemplateObject(keys, row);
    }
    if (row && typeof row === 'object') {
      return rowToTemplateObject(
        keys,
        keys.map((key, index) => row[key] ?? row[headers[index]] ?? '')
      );
    }

    return { c1: String(row ?? ''), cells: [{ key: 'c1', text: String(row ?? '') }] };
  });
}

function headersToTemplateObject(headers) {
  return Object.fromEntries((headers || []).map((header, index) => [`c${index + 1}`, header || `列${index + 1}`]));
}

function columnsToTemplateObjects(headers) {
  return (headers || []).map((header, index) => ({
    key: `c${index + 1}`,
    index: index + 1,
    text: header || `列${index + 1}`,
  }));
}

function rowToTemplateObject(keys, values) {
  const effectiveKeys = keys.length ? keys : values.map((_, index) => `c${index + 1}`);
  const entries = effectiveKeys.map((key, index) => [key, values[index] ?? '']);
  return {
    ...Object.fromEntries(entries),
    cells: entries.map(([key, value], index) => ({
      key,
      index: index + 1,
      text: value,
    })),
  };
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

function readableTableTitle(table = {}, index = 0, sectionTitle = '') {
  const explicit = String(table.title || table.caption || '').trim();
  if (explicit && !isGenericTableTitle(explicit, index)) {
    return stripTableNumberPrefix(explicit);
  }

  const context = cleanSectionTitle(sectionTitle);
  if (context) {
    return /表$/.test(context) ? context : `${context}表`;
  }

  const headers = (table.headers || [])
    .map((header) => String(header || '').trim())
    .filter(Boolean)
    .slice(0, 3);
  if (headers.length > 0) {
    return `${headers.join('、')}表`;
  }

  return `表格 ${index + 1}`;
}

function readableFigureCaption(figure = {}, index = 0, sectionTitle = '') {
  const explicit = String(figure.caption || figure.title || '').trim();
  if (explicit && !isGenericFigureCaption(explicit, index)) {
    return stripFigureNumberPrefix(explicit);
  }

  const context = cleanSectionTitle(sectionTitle);
  if (context) {
    return context;
  }

  return `图示 ${index + 1}`;
}

function isGenericTableTitle(value, index = 0) {
  const text = String(value || '').trim();
  return new RegExp(`^表格\\s*${index + 1}$`).test(text) || /^表格\s*\d+$/.test(text) || /^表\s*\d+$/.test(text);
}

function isGenericFigureCaption(value, index = 0) {
  const text = String(value || '').trim();
  return new RegExp(`^(图|图片|图示)\\s*${index + 1}$`).test(text) || /^(图|图片|图示)\s*\d+$/.test(text);
}

function stripTableNumberPrefix(value) {
  return String(value || '').trim().replace(/^表\s*\d+\s*[:：、.．-]?\s*/, '').trim();
}

function stripFigureNumberPrefix(value) {
  return String(value || '').trim().replace(/^图\s*\d+\s*[:：、.．-]?\s*/, '').trim();
}

function cleanSectionTitle(value) {
  return String(value || '')
    .trim()
    .replace(/^第[一二三四五六七八九十百千万0-9]+[章节篇部分]\s*[、:：.]?\s*/, '')
    .replace(/^[一二三四五六七八九十百千万]+[、.．]\s*/, '')
    .replace(/^\d+(?:\.\d+)*[、.．]?\s+/, '')
    .replace(/[（(]\s*(?:C4Context|flowchart|graph|sequenceDiagram|Mermaid|SVG|PNG)[^)）]*[)）]/gi, '')
    .trim();
}

function assertValidDomainObject(schemaName, value) {
  const result = validateDomainObject(schemaName, value);
  if (!result.ok) {
    throw new Error(`${schemaName} validation failed: ${JSON.stringify(result.errors)}`);
  }
}

module.exports = { buildRenderPlan };
