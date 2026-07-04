function validateXmlWellFormed(xml, label = 'XML') {
  const source = String(xml || '');
  if (!source.trim()) {
    return { ok: false, message: `${label} is empty.` };
  }

  const stack = [];
  const tagPattern = /<[^>]+>/g;
  let match;
  while ((match = tagPattern.exec(source))) {
    const tag = match[0];
    if (shouldSkipTag(tag)) {
      continue;
    }

    const closing = tag.match(/^<\s*\/\s*([A-Za-z_][\w:.-]*)\s*>$/);
    if (closing) {
      const name = closing[1];
      const open = stack.pop();
      if (!open) {
        return {
          ok: false,
          message: `${label} has unexpected closing tag </${name}> at index ${match.index}.`,
        };
      }
      if (open.name !== name) {
        return {
          ok: false,
          message: `${label} has mismatched tag at index ${match.index}: expected </${open.name}> but found </${name}>.`,
        };
      }
      continue;
    }

    if (/\/\s*>$/.test(tag)) {
      continue;
    }

    const opening = tag.match(/^<\s*([A-Za-z_][\w:.-]*)\b/);
    if (opening) {
      stack.push({ name: opening[1], index: match.index });
    }
  }

  if (stack.length > 0) {
    const open = stack[stack.length - 1];
    return {
      ok: false,
      message: `${label} has unclosed tag <${open.name}> opened at index ${open.index}.`,
    };
  }

  return { ok: true };
}

function shouldSkipTag(tag) {
  return /^<\?/.test(tag) ||
    /^<!--/.test(tag) ||
    /^<!\[CDATA\[/.test(tag) ||
    /^<!/.test(tag);
}

module.exports = { validateXmlWellFormed };
