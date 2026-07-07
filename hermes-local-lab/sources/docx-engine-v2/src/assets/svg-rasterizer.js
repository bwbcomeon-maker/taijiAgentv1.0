const fs = require('node:fs');

let Resvg;

function rasterizeSvgToPng({ svgText = '', svgPath = '', pngPath = '', width = 0 } = {}) {
  if (!pngPath) {
    throw new Error('缺少 PNG 输出路径');
  }
  const sourceText = sanitizeSvgText(svgText || (svgPath ? fs.readFileSync(svgPath, 'utf8') : ''));
  if (!sourceText.trim()) {
    throw new Error('缺少 SVG 内容，无法生成 PNG 展示图');
  }

  const dimensions = svgDimensions(sourceText);
  const targetWidth = rasterWidth(width || dimensions.width);
  const resvg = new ResvgCtor(sourceText, {
    background: 'white',
    fitTo: {
      mode: 'width',
      value: targetWidth,
    },
  });
  fs.writeFileSync(pngPath, resvg.render().asPng());
  if (!fs.existsSync(pngPath) || fs.statSync(pngPath).size === 0) {
    throw new Error(`PNG 展示图生成失败: ${pngPath}`);
  }
  return {
    width: targetWidth,
    height: Math.round(targetWidth / Math.max(0.01, dimensions.aspectRatio || 16 / 9)),
    sourceDimensions: dimensions,
  };
}

function ResvgCtor(...args) {
  if (!Resvg) {
    try {
      ({ Resvg } = require('@resvg/resvg-js'));
    } catch (error) {
      throw new Error(
        [
          '缺少跨平台 SVG 转 PNG 依赖 @resvg/resvg-js，不能生成稳定的 PNG 展示图。',
          '请使用随 docx-template-skill 一起打包的 engine/node_modules，或在 docx-engine-v2 中执行 npm install。',
          `原始错误: ${error.message}`,
        ].join(' ')
      );
    }
  }
  return new Resvg(...args);
}

function rasterWidth(width) {
  const numeric = Number(width);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return 1920;
  }
  return Math.max(1600, Math.min(3200, Math.round(numeric * 2)));
}

function svgDimensions(svgText = '') {
  const normalizedText = sanitizeSvgText(svgText);
  const svgTag = rootSvgTag(normalizedText);
  const width = numericSvgAttribute(svgTag, 'width');
  const height = numericSvgAttribute(svgTag, 'height');
  if (width > 0 && height > 0) {
    return {
      width,
      height,
      aspectRatio: width / height,
      unit: 'px',
    };
  }

  const viewBox = String(svgTag || '').match(/\bviewBox=["']\s*[-0-9.]+\s+[-0-9.]+\s+([0-9.]+)\s+([0-9.]+)\s*["']/i);
  const viewBoxWidth = viewBox ? Number(viewBox[1]) : 0;
  const viewBoxHeight = viewBox ? Number(viewBox[2]) : 0;
  if (viewBoxWidth > 0 && viewBoxHeight > 0) {
    return {
      width: viewBoxWidth,
      height: viewBoxHeight,
      aspectRatio: viewBoxWidth / viewBoxHeight,
      unit: 'px',
    };
  }

  return {
    width: 960,
    height: 540,
    aspectRatio: 16 / 9,
    unit: 'px',
  };
}

function rootSvgTag(text) {
  const match = String(text || '').match(/<svg\b[^>]*>/i);
  return match ? match[0] : '';
}

function numericSvgAttribute(text, name) {
  const match = String(text || '').match(new RegExp(`\\b${name}=["']([0-9.]+)`));
  return match ? Number(match[1]) : 0;
}

function sanitizeSvgText(svgText = '') {
  return String(svgText || '').replace(
    /&(?!#\d+;|#x[0-9A-Fa-f]+;|amp;|lt;|gt;|quot;|apos;)/g,
    '&amp;'
  );
}

module.exports = {
  rasterizeSvgToPng,
  sanitizeSvgText,
  svgDimensions,
};
