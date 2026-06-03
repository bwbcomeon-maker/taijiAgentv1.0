---
name: web-article-extractor
description: Use when the user wants to extract online article text, especially WeChat/公众号 or web articles, for the Hermes Writing Agent workflow.
version: 0.6.0-hermes.1
author: xue1127/writing-agent, ported for Hermes Local Lab
license: MIT
metadata:
  hermes:
    tags: [writing, extraction, web, articles, wechat, writeflow]
    related_skills: [workflow-producer, style-modeler]
    source_repo: https://github.com/xue1127/writing-agent
    source_commit: a296f1cdfb88887b336f8dd2776257c18acab99d
---

# Web Article Extractor for Hermes Writing Agent

## Overview

Extract article text into workspace Markdown for later style modeling,
research, or writing. This is adapted from the upstream `公众号文章获取` skill,
but it must use Hermes tools instead of Claude Code MCP tool names.

Preferred archive path:

```text
articles/_source/YYYY-MM-DD-title.md
```

## Tool Mapping

- Use `web_extract` for ordinary public article pages.
- Use `browser_navigate`, `browser_snapshot`, `browser_get_images`, and
  `browser_cdp` when the page is rendered dynamically or `web_extract` misses
  the article body.
- Use `terminal` only for local text cleanup or bundled scripts under this
  skill's `scripts/` directory.
- Do not claim a login-gated or anti-scraped page was extracted if the body was
  not actually obtained.

## Extraction Steps

1. Open or fetch the URL.
2. Extract title, author/source, publish date when available, canonical URL, and
   article body.
3. Remove navigation, comments, ads, unrelated recommendations, and duplicate
   boilerplate.
4. Save Markdown with front matter:

```markdown
---
title: ...
source: ...
author: ...
url: ...
retrieved_at: ...
---

# Title

Article body...
```

5. Return the saved path and a short extraction quality note.

## WeChat / 公众号 Notes

WeChat pages often block non-browser extraction. If `web_extract` fails, try
browser tools. If browser access still does not expose the full article, tell
the user what was visible and what is missing. Do not fabricate the hidden body.

## Bundled Files

This skill includes the upstream extraction scripts and references for manual
fallbacks. Use them only when the regular Hermes web/browser tools do not
produce clean text.
